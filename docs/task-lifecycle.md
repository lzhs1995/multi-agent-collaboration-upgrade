# Retained task lifecycle

`upgrade_live_control.task_lifecycle(runtime_ref, client)` retains the existing
external owner's pinned TaskRPC and starts the same-runtime read-only observer.
It does not launch clients, release a held experiment, create a task, publish
adoption or grant business operations. It accepts only the explicit TASK startup
and observer network contract. Its exclusive evidence directory cannot restart.

The same object performs these separate guarded actions:

1. `bootstrap()` calls the original one-shot task creation and attach publisher.
2. After actual TUI attachment, `attach_readiness()` attaches the original
   readiness-only actor. Bounded `poll_readiness()` calls retain its SQLite ledger,
   RPC and observer. Only an original normal completion with no unresolved call
   reaches the adoption phase.
3. `adopt()` uses the existing external inspector/publication gate. `NOT_READY`
   leaves the owner intact. Full TASK adoption remains unimplemented and refused.
4. After actual adoption, `attach_campaign()` passes the same RPC and ledger to
   the original business attachment gate. It transfers the original normal
   completion to the new service, whose scope excludes `ready`, before enabling
   the explicit busy/idle callback ingress. The first business input therefore
   uses the existing next-turn contract instead of waiting on an ended turn.
5. Bounded `run()` calls keep the observer alive across yields. Normal scope
   exhaustion or failure closes the callback endpoint, ledger and original RPC;
   it stops the observer without reopening any of them.

The owner is bound to its process and execution thread/task. An interrupted or
unknown mutation is preserved, never retried. STOP, dead observer and original
peer changes refuse further work. Closing does not send interrupt, input or
callback and does not delete evidence. A synchronous observer read can outlive
its one-second stop join; the close result reports this explicitly and does not
claim hard cancellation.

Tests exercise real observer thread/Unix requests, the readiness actor and
SQLite, terminal handoff and callback ingress. Creation, adoption and deployment
observations remain explicit fixtures. No live model, original Claude callback,
hook adoption or business acceptance follows from those tests. The driver still
needs the complete adoption producer/private reader, remaining runtime boundary
checks and an authorized new launch with fresh capacity/provider readiness.
