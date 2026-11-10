# Restricted continuity work and results

This additive candidate publishes a bounded file handoff and a new immutable
text artifact for the original task. It does not install itself into a running
TASK service, start a model, send terminal input, execute a command, release a
lease, rewrite an original artifact or establish a successful live takeover.

The separate extension loader must issue its authenticated `ExtensionContext`,
or the fixed independent Claude route below must issue its exact `PeerContext`.
Calling the handler with a dictionary or an arbitrary Python object is refused.
The context binds the original registration, adopted sessions, authority,
current native invocation, one consumed operation and controller-owned output
root. This is a trusted controller boundary, not a Python security sandbox.
Experimental agents must remain kernel-denied writes to the controller tree.

`validate_operation({action, ...payload}, registration)` validates the frozen
operation. `dispatch(action, payload, context)` implements:

| Action | Fixed payload | Effect |
| --- | --- | --- |
| `continuity-work` | `contract`, `scope_id` | Verify original native continuity outcomes, invoke the pinned original planner, reserve one original scope, publish its inputs/handoff and result template. |
| `continuity-result` | `contract`, `scope_id`, `work_operation_id`, `statement`, `review_statement` | Require complete native output of the original work operation; read original model declarations; publish one new scoped UTF-8 artifact and result. |

Use `null` for the statement references to discover the unique native statement
in the original post-work window. The frozen catalog need not invent a future
record reference. The loader independently checks assistant role, session,
current Codex turn, exact task/extension/operation and invocation boundary.

The contract has schema `upgrade-task-continuity-contract-v1` and exact fields
`task_id`, `base_registration`, `original_authority`,
`continuity_registration`, `budget`, `checkpoint`. All references are complete
path/bytes/SHA pins. The original advisory registration is unchanged: the
planner still grants no takeover permission. The separately accepted extension
scope authorizes only the new file work described here.

Work returns every input and the handoff as complete UTF-8 text with its original
reference, bounded to 128 KiB in total. Binary inputs are refused. Result
publication requires the complete original native work output, so paths/hashes
alone never establish body consumption. Handoff content is preserved but is
not interpreted as permission: permission comes from the original authority and
the exact extension scope acceptance.

The budget schema is `upgrade-task-continuity-budget-v1` with `task_id`,
`max_subagents: 2`, `child_budget_scope: "WHOLE_TASK"`, `children_used`,
`review_limit: 3`, `review_rounds_used`, `review_history`. History references are
read in full. This handler creates no children and grants no child creation;
it preserves the original used identities and rounds. It cannot monitor child
creation performed outside this controller.

The checkpoint schema is `upgrade-task-continuity-checkpoint-v1` with
`task_id`, `base_registration`, `budget`, and `scopes`. Each scope has exactly
`scope_id`, `mode`, `writer_lane`, `output_name`, `inputs`, `handoff`,
`nonce_state`, `lease_state`. The output name is one bounded filename, never an
arbitrary path. `CODEX_SOLO` has the original Codex writer; `CLAUDE_PEER` has one
original Claude writer and the other original Claude as reviewer.

The nonce/lease records have schema `upgrade-task-continuity-guard-v1`, exact
`task_id`, `scope_id`, `kind`, `state`, `pending`. The only accepted states are
`nonce_state: CLEAR` and `lease_state: NONE`, with `pending: []`. Changes to the
pinned records fail before publication. Unknown or active resources are never
released or repaired. These are explicitly accepted controller snapshots;
**no production resource-state producer or online resource lock integration is
wired here**. Consequently this entry supports isolated file artifacts only,
not Word, Zotero, shared databases or takeover of another writer's live files.

All extensions under the same base registration share `continuity-ledger`.
Its exclusive lock serializes scope claims and review spending. Its immutable
anchor fixes the original contract and budget; changing the extension ID does
not renew them. Each scope has one work claim and one result attempt. The
result intent is durable before the artifact write. A crash after intent,
after artifact creation or before the review commit remains UNKNOWN and blocks
another attempt. Original records stay available for read-only reconciliation;
there is no automatic repair, release or replay operation.

The result model emits `upgrade-task-continuity-submission-v1` with
`task_id`, `extension_id`, `operation_id`, exact `work` reference, `text`,
`self_review`, and `limitations`. The text limit is 128 KiB. A Claude peer also
emits `upgrade-task-continuity-review-v1` with the same task/extension/operation
and work binding, `artifact_sha256`, `verdict` (`accept`, `revise`, or `reject`),
and `reason`. Every valid peer verdict consumes the next retained review round,
including rejection. It never opens another review of the same scope.

Codex output is labelled `solo_self_review`. Claude output is labelled
`claude_peer_review`, with its actual verdict and `peer_accepted` flag. Neither
label means scientific or formal business acceptance. Original limitations are
carried into the result, and `formal_business_accepted` and
`live_transport_verified` remain false.

## Independent original Claude request route

The current ExtensionLoader requires the original Codex RPC and an in-progress
Codex turn. Reading Claude declarations through it is not independent peer
execution. The new `scripts/upgrade_task_claude_continuity_route.py` supplies a
separate fixed API:

```python
permission = scope_value(extension_ref, scope_id)
# Original Codex emits this exact native assistant JSON, then normally completes
# that same turn. Registration precedes any subsequent work or failure.
route_ref = register_peer_route(extension_ref, scope_id,
    acceptance=original_native_assistant_ref, terminal=original_task_complete_ref)
owner = PeerRoute(route_ref)  # explicit retained-controller Unix listener
owner.serve_once(timeout=5)   # one fixed request; no model RPC or spawned worker
```

Registration is inert and requires the original live publisher identity. It
freezes the original authority, two distinct existing Claude process identities,
exact work/result operations, terminal and native journal fences, sources and
Python executable. It cannot be retroactively approved by a disconnected Codex.
After registration, requests need no Codex live RPC, new turn or further
approval. Original native continuity outcome validation still runs before
work/result publication. An unavailable model is not authority to release
resources or create sessions.

The original startup stop path and, when adopted, the stage stop path are derived
from their pinned authority and frozen into the route. Every execute checks
these paths before its request intent and on context rechecks, including directly
before work, artifact and result publication. A stop appearing after an intent
preserves UNKNOWN and never triggers replay. These file checks need no healthy
Codex RPC. Read-only evidence remains inspectable after a stop. As with the
original controller gates, stop checks and filesystem publication are sequential
checks, not an atomic transaction with an external stop writer.

The retained owner explicitly runs this listener. Each original Claude uses its
native Bash tool with the exact command returned by
`command(route_ref, lane, verb, operation_id)`. The CLI accepts only `request`
and fixed route hash, lane, verb and operation arguments; no arbitrary argv.
Six once-only slots are enrolled: writer work, writer result, reviewer read-work,
reviewer read-submission, and each peer's read-result.

The route reuses the callback protocol's real `caller_ancestry`, kernel peer,
process and socket primitives. It does not reinterpret an existing callback
pack as work permission. The accepted socket must descend from the registered
original Claude process and match its actual native Bash call. The client
verifies the actual server process and descriptor **before sending** a request.
Request and ACK frames each have a one-second total deadline, including partial
reads. Only the active, exact PeerContext may dispatch; a JSON dictionary or
copied attributes cannot authenticate a context.

Work returns the complete original input and handoff. Both peers' complete
original Bash tool-result records must contain that exact response. The reviewer
must also consume the writer's complete original native submission before its
hash-bound review. The review's native record must begin at or after the end of
that complete read-submission output; a review written first cannot be accepted
by filling in the read afterward. Result delivery and read-result are separate: read-result
requires the writer's original complete native result. Its own consumption can
then be checked using `owner.consumed`. Socket delivery/ACK alone never proves
that a model consumed a result.

An exception after a durable request intent records UNKNOWN and blocks every
further execute operation. The same slot never replays. Read-only requests may
inspect original evidence only after the required original complete native
output exists; each response retains prior UNKNOWN references and does not
clear them. A lost result ACK without native output refuses read-result.
RECORDED means the selected operation or read receipt exists, not consumption or
formal acceptance. There is no automatic recovery, lease release, hidden retry
or restart. If the owner process itself dies, this retained-process endpoint
cannot silently rebind or resume.

Tests use real local Unix sockets, kernel PIDs and ancestry of two ordinary
Python fixture parents and their request children. Those checks are not mocked.
The model journals, pre-registration authority loader and native continuity
evaluator are synthetic and explicitly replaced in tests. This proves the
bounded independent request/file workflow without contacting Codex; it does
**not** prove real Claude model participation or live adoption. No j14 artifacts,
active controllers, shared skills or running sessions were changed.

The core previously passed 19 distinct fixture cases across three focused runs.
The peer route's first six-case run preserved three failures caused by querying
the macOS peer PID after the server had closed its socket. Those three passed
after moving the mandatory peer check before sending and retaining the original
process/descriptor check after the final ACK. Exact later review tests and
outcomes are retained in `task-continuity-validation.json`.

Run from this repository's scripts directory:

```bash
python3.14 -B -m unittest -v test_upgrade_task_continuity_capability
python3.14 -B -m unittest -v test_upgrade_task_claude_continuity_route
```
