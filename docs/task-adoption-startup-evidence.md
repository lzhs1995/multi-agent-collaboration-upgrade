# Original TASK startup evidence

TASK's execution server starts once from the external release caller, after the
original held release succeeds. It applies the complete pinned server Seatbelt
profile directly. Starting it inside the already sandboxed terminal applies
Seatbelt twice and fails on macOS with `sandbox_apply: Operation not permitted`.
The terminal waits for the original server result, then follows the existing
TUI-first connection and adoption sequence.

The external launch binds the active permit, exact release/receipt, held lanes,
retained owner and runtime pins. It closes inherited descriptors and preserves
the exclusive launch intent; failed or uncertain attempts cannot be retried.
Owner and driver inspect a recorded server failure before waiting for a TUI
origin record, because that record cannot arrive after failed server startup.
Socket creation alone remains insufficient to establish native adoption or a
completed task. The external launcher is not installed in model-facing private
controllers, and existing stopped attempts remain stopped.

The TASK inspector now produces `task_startup_evidence` through
`upgrade_task_adoption_evidence.collect` when the explicit requester-descriptor
contract is enabled and the three original startup readers pass. The same
module is included in the generated private controller. Its `verify` function
recomputes the evidence instead of trusting a copied ready flag.

The reader binds the four original launch records, distinct display and
execution-server PIDs, original task creation and journal, authenticated RPC
peer observation, writable journal descriptor, three native readiness results,
and automatic PreToolUse/PostToolUse observations. The readiness readers also
recheck the original observer receipts and requester FD/fileport evidence.
A changed launch, writer, session, hook result or native readiness result fails
the join. Failed joins make the inspector's startup status false.

These are historical startup checks. The reader does not connect to RPC, send
input, execute hooks or publish adoption. It explicitly reports no live-process,
business-adoption or whole-client-isolation authority. Formal launch and input
adoption refusals remain until the complete producer and private consumer have
the remaining runtime evidence. An inspector ready flag cannot waive that work.

Validation uses original filesystem records and generated private modules with
no source import fallback. Native client/process/RPC/readiness fixtures in the
join tests are labeled and do not count as real model collaboration. The
existing observer/native-readiness tests remain separate evidence. No installed
controller, prior experiment, client session or original attempt is migrated.
