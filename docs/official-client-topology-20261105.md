# Official Codex startup: observed gaps and next implementation boundary

Status: the original j7 pilot ended with startup TIMEOUT on 2026-11-04 at
19:10:07 UTC. One permit and one release were consumed. Business acceptance
remains 0/33 and paired acceptance 0/15. None of the changes in this worktree
applies to the frozen j7 controller or authorizes replaying that attempt.

## Confirmed behavior

All observed Codex primary processes use the selected official 0.164.0 package.
The main maintenance session successfully recovered its original session and
native identity. The experimental TUI is a separate failure: its remote server
owns the rollout, while the TUI does not. The private startup environment still
requires a legacy native foreground-observer file which the official binary
does not produce. Removing the requirement alone would not authenticate that
TUI's current selected thread.

The exact official source does contain the experimental
`thread.canAcceptDirectInput` field. A read-only request to the original j7
server with `experimentalApi: true` returned `canAcceptDirectInput: true` for the
original thread. The stable generated schema omits the field; that omission
must not be used to infer runtime absence. The original capability check stays.

This establishes server/thread input capability, not current TUI selection,
task acceptance, a native user receipt, or permission to adopt the failed run.

Sources use official commit `6aaac694803004a63c6f393d153c96d22521a6b9`:

- `codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs:272` declares the
  experimental field.
- `codex-rs/tui/src/app.rs` and `app/thread_routing.rs` keep active selection in
  TUI-local state.
- `codex-rs/app-server/src/in_process.rs` uses internal channels for embedded
  operation; it does not provide an external socket for that same session.
- `codex-rs/tui/src/bottom_pane/chat_composer.rs:130` distinguishes immediate
  Enter submission from Tab queuing. This is source evidence, not a live
  demonstration of all busy/Goal/compaction conditions.

## Implemented correction in this worktree

Codex tool shells now receive an explicit private `TMPPREFIX` through
`shell_environment_policy.set`. zsh defaults to `/tmp/zsh` for here-documents
even when `TMPDIR` points into the private arm. The private `native_root/tmp`
directory already exists and is inside the current Seatbelt write boundary.
No global writable path, sandbox exception, or client binary change is needed.

A real macOS Seatbelt regression reproduces the old failure, checks the full
here-document output after the change, and verifies that an outside sentinel
still cannot be overwritten. The configuration, private module closure and
startup suites pass 33 tests. This does not establish a complete startup fix.

## Remaining complete correction

Evaluate a separately registered task/thread contract for future remote runs:

1. Freeze the task, source, exact TUI and server launches, PID/birth/executable,
   endpoint UUIDs, private roots and actual socket peer identity.
2. Enroll the original native thread using the exact original startup prompt,
   real probe and automatic-hook records, original writable rollout and live
   server metadata. A filename, environment selector, latest record or sole
   server thread alone is insufficient.
3. Explicitly name the authority as a task bound to that server/thread. Do not
   expose it as proof of the TUI's currently selected thread. A UI selection
   change must not transfer task ownership or select a different send target.
4. Verify caller provenance separately from receiver enrollment, including
   native hook ancestry. Reject ambiguous writer/caller cases; an inherited
   root selector cannot confer root ownership on a child.
5. Keep genuine `turn/start`/`turn/steer`, exact active-turn binding, original
   input intent, no retry after UNKNOWN, native original-session receipts and
   full body consumption. No synthetic foreground file or capability flag.
6. Give startup, adoption, handshake, callback, takeover and recovery explicit
   checks for this new authority scope. Prevent silent use by legacy contracts.
7. Run a bounded real capability stage before registering another full matrix:
   original startup, tools/hooks, task identity, bidirectional input, native
   receipts/consumption, busy/Goal/compaction, drafts, failure and recovery.
   Only after that stage passes may the unchanged 33/15 business matrix resume
   under a new registration. Preserve old attempts, deadlines and costs.

The original independent S40 and Claude consultations found this task-thread
design viable, with the enrollment/caller/visibility gaps listed above still
open. It is not implemented or activated as a complete transport.

The new `upgrade_rpc_peer.py` implements one narrow prerequisite: kernel peer
PID and uid plus an exact server incarnation/executable/argv check on the
connected Unix socket, with a second read to catch drift. Local socket tests
cover a different same-user server, changed birth, executable and argv, and
mid-read replacement. A read-only diagnostic against the actual original j7
server confirmed peer PID 46405 and the pinned birth before and after real
`thread/read`. The result explicitly says `foreground_authentication=false`
and `enrollment_proven=false`. This module is not connected to any legacy path.

Separately, the original frozen readiness parser correlated the exact native
startup prompt, one successful Codex probe, its nested command result and
automatic PreToolUse/PostToolUse records against the server-owned original
rollout. That is historical evidence for the new design; it neither supplies
a current invoking-tool proof nor changes j7's TIMEOUT or absent adoption.

If its required evidence cannot be obtained with the official client, the
alternative is an explicitly registered embedded topology with guarded
terminal input. That alternative must report native RPC as unavailable and
must not be counted as native-RPC acceptance or silently replace a requirement.

The overall deliverable remains cmux external coordination plus Herdr internal
collaboration, pure-cmux compatibility, both existing skills, three-state
Codex/Claude continuity, 60-second requests, child result return, real thesis
work and measured efficiency. Infrastructure checks do not satisfy it.

## Exact thread reader and remaining actor gap

`upgrade_task_thread.py` now reads a controller-supplied exact thread anchor.
It checks the kernel socket peer on the same RPC connection, socket identity,
server's writable descriptor for the named journal, original metadata and exact
startup user record, and real `thread/read` / `thread/turns/list`. Extra open
journals do not select a different thread; duplicate descriptors for this journal,
file replacement, record changes, a changed server and missing input capability
are refused. The result explicitly grants no caller, foreground or enrollment
authority. Startup probe/hook completion and task authority remain separate.

Fourteen local tests cover actual sockets/files with fixture RPC/process data.
A separate read-only invocation against original j7 server 46405 succeeded with
the original thread and startup record. No input or j7 state change occurred.
The first live diagnostic refused because remote TUI metadata says
`source=vscode`, `thread_source=user`; the new reader now accepts both official
`cli` and `vscode` source labels while still requiring a user thread. Official
`protocol.rs:2959` defines VSCode as the session-source default. Source labels
alone never authenticate a caller. The first failure remains preserved.

S40's independent review found no blocking defect in the isolated peer primitive,
but confirmed that synchronous PreToolUse rewrites race by completion order and
hook failures can leave the original command unchanged. A copyable one-use
token therefore cannot serve as complete actor proof. A subsequent real negative
control also ruled out authenticating a hook merely from a direct native parent
and buffered stdin pipe: the official writer closes immediately, and a normal
tool can reproduce that process/pipe shape. Neither proposed hook actor is
installed. The next investigation is the official server-origin dynamic-tool
request on a kernel-authenticated connection, with explicitly registered task
operations and separate controller ownership; it is not yet an implementation.

The original Claude's later independent peer review added four qualifications,
now applied: Darwin's LOCAL_PEERPID follows the peer's most recent I/O, so the
reader verifies the peer immediately after each response; socket transfer,
socket activation and multiprocess socket I/O are outside this contract. Linux
SO_PEERCRED is connection-time metadata and cannot establish that property.
All peer/reader exceptions now refuse proof without granting input retry.
Sixteen focused tests pass, including the actual Darwin process reader and
a real accepted-socket transfer to a child whose response is refused. Finally,
file_identity denotes path stat metadata rather than a mapped-binary content
hash; official package/signature verification remains a separate requirement.

`upgrade_dynamic_route.py` adds an optional protocol router for a candidate relay.
It reserves `upgrade_collaboration` calls for the controller on one upstream
connection; original TUI tools/approvals and notifications remain TUI traffic.
It rejects another thread, reused request/call IDs, a TUI response to a reserved
call, unknown response owners and retry after intended response or disconnect.
Eight tests exercise routing, ID namespace collisions and refusal behavior.
This is not a live relay, durable operation ledger or actor enrollment.

The independent S40 review corrected the routing premise: official external
TUI `app_server_events.rs:622–628` returns without responding when the namespace
is not `codex_tui`. Consequently a relay is not required merely to prevent the
official TUI from answering our custom tools. The router remains an unactivated
candidate. The server still lacks an exclusive dynamic responder; an unrelated
connection knowing an ID can consume its callback. Exact peer verification
alone does not prevent that. The next minimal deployment separates the remote
TUI's connection permission from the app-server/agent descendants, denies those
descendants connecting to the protected socket, and records original operation
intent/result plus exact native completion. Trusted unsandboxed operators remain
outside the adversarial agent boundary; no global same-user isolation is claimed.

## New bounded-operation candidate and first real diagnostic

`upgrade_task_operations.py` records an exclusive operation intent before any
external effect. The model selects only a controller-declared operation ID for
one exact task/thread/turn. Operation and request IDs cannot be reused after
restart, cancellation or UNKNOWN. Results, original external receipts and one
response intent are persisted separately. An exact authenticated native result
can settle an unknown response; a competing/mismatched result becomes CONFLICT.
Neither result settlement nor socket write claims subsequent model consumption.
Ten tests include actual competing processes and a process that exits after its
external effect, before recording the result.

`upgrade_task_socket_policy.py` is a direct-connect restriction, not complete
agent confinement. Two tests include a real Darwin server accepting controller
input, refusal of descendant direct/alias/rename/hardlink paths, and functioning
unrelated IPC/TCP. The independent review identified additional deployment paths:
cmux/Herdr command brokers, remote execution environments and inherited FDs.
These remain prerequisites to full experimental admission. Production channels
and old profiles have not been changed. The network rule now uses the official
`remote unix-socket` filter. A fresh socket must be absent before launch because
official startup tests for an existing server by attempting a self-connection.

`upgrade_task_rpc.py` retains server requests that arrive while waiting for a
client response, checks the exact kernel peer on receives and sends, and closes
its socket even when peer verification refuses cleanup. Three tests cover
opposite-direction ID collisions, refusal before write, and failure cleanup.
The controller still supplies genuine enrollment and durable send authority.

The first separately scoped real diagnostic used the same official binary and
the unchanged agentrouter-a / 3465 / gpt-6-astra route. Official server startup,
experimental initialize, thread/start with dynamicTools and turn/start succeeded.
The upstream gateway then returned HTTP 401 "unauthorized client detected";
no dynamic operation occurred, no native tool result or model consumption passed.
This is an upstream client-acceptance failure, not evidence of duplicate local
Codex installations. Its original attempt and 180-second budget are retained;
it must not be replayed. A controller-versus-TUI client-origin difference is an
investigation hypothesis, not a proven cause. No client identity/header was
forged, no route was substituted, and no business item was accepted. The live
run used the RPC code before the cleanup-only correction; that correction has
separate local regression evidence.

## First successful official TUI and dynamic-operation round trip

A distinct diagnostic (`native-dynamic-smoke-v5`, private root `/Users/lzhs/d5`)
passed on the same official 0.164.0 binary, agentrouter-a gateway on port 3465,
and gpt-6-astra. The actual official remote TUI initialized the new server first;
the helper kept its own `cmux_native_input` identity. The new thread's observed
originator was `codex-tui`. No header override or provider configuration change
was used. A TUI was attached to that exact thread after its first turn persisted
the journal. The real model called the registered dynamic tool once, the
controller recorded and performed one fixed diagnostic operation, and its exact
result appeared in native completion, the final assistant message and the
attached TUI. All 55 recorded RPC messages passed the pinned-server peer check.
The three diagnostic processes were stopped after the result; the server exited
normally. The maintenance ROOT, S40 and original Claude sessions were unchanged.

The accepted record is
`/Users/lzhs/.local/state/codex-toolchain/unification-20261104/official-next-preparation-20261105/j7-startup-diagnosis/ROOT-NATIVE-DYNAMIC-V5-ACCEPTANCE.json`.
It pins the original user/assistant journal slices, single server request and
response intent, completion and TUI bytes. This is one bounded capability
diagnostic; it does not satisfy the 33 business items or 15 paired comparisons.

Intermediate attempts remain intact: v2/v3 refused at the diagnostic's incorrect
TUI readiness parser (zero model turns); v4 created a thread but the official
TUI could not resume it before any turn had persisted a rollout (zero model
turns). Readiness matching was checked against both captured official PTYs before
v4. The v5 sequence waits for the first-turn journal before attaching. These
failures do not authorize replaying any old attempt. The v1 401 and v5 success
support the startup-order correction but do not establish the provider's exact
client acceptance policy.

Integrating this sequence requires an explicit new topology contract, not an
implicit exception to old foreground rules: launch the official TUI first;
register dynamic tools on a new task thread; pin the original server/thread,
startup input and native result; then attach the same thread in the TUI. Preserve
the helper identity. Complete agent confinement still requires actual broker,
remote-environment and inherited-FD checks in all three lanes. Busy input,
cancellation, disconnection, recovery and the existing adoption/handshake/callback
interfaces still need their own live acceptance before a new full matrix starts.

The routing issue comes from official `bespoke_event_handling.rs:1100` and
`outgoing_message.rs:347,578`: dynamic calls target subscribed connections, and
exclusive callback ownership is enforced for user-verification ceremonies,
not ordinary dynamic calls. Merely attaching another controller beside the
TUI does not establish exclusive response ownership. Any relay integration
must protect its only upstream socket, authenticate its exact downstream TUI,
preserve other approval traffic, record real native invocation/completion, and
retain original UNKNOWN attempts on failure. A sole connection and model
arguments alone do not establish task authorization.

## Generated idle callback integration (2026-11-06)

New generation from the immutable original .84 controller wraps only its fresh
callback submission. The original journal retains its delivery, pane and target
locks. A TASK callback observed at idle adds the exact pack, original attempt
and enrolled ingress descriptor to its native request, persists one original
input intent/fence, then calls the retained campaign service through the bounded
Unix ingress. It never directly calls TASK `turn/start`. Active `turn/steer` and
legacy submission retain their original paths. Transport errors consume the
original attempt; neither a retry nor terminal fallback is enabled.

Historical readers resolve the static descriptor from the original startup.
They join the sender's immutable pre-send attempt, exact callback text/request,
original Claude process ancestry, retained service exchange, enrollment result
and the original SQLite turn with its preceding turn. The generated .84 native
reader, original callback publisher and campaign delivery verifier all require
this provenance for idle input. Missing provenance remains unconfirmed even if
the transport returned successfully. Native user reception and model body
consumption remain separate evidence.

The sender tests execute the generated original journal/input code with real
file locks and native fences. Separate route tests execute the retained service
and SQLite ledger. Transport/model returns, deployment observations and receiver
authorization are explicit fixtures; reader wiring tests spy on the separately
tested route verifier. These checks establish component behavior, not one live
end-to-end callback, automatic adoption, recurring 60-second requests or formal
business acceptance. Process ancestry authenticates a process tree, not one
model invocation. The full actor-aware deployment and sender attribution still
need their own integration and live checks. No existing controller, attempt,
session or terminal experiment is migrated by generating this candidate.
