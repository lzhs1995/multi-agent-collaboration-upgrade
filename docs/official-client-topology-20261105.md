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
