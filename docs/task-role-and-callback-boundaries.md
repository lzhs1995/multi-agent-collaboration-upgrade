# Explicit task roles and callback network scope

The remote Codex terminal and its app-server are separate processes. A live
terminal does not prove the execution server or original task journal survived.
`upgrade_task_roles.verify_roles` checks the original four process incarnations:
the display, the app-server and both Claude executors. It matches the frozen
launch records, executable/argv, native roots, endpoint UUIDs and original task
journal. It rechecks the same processes and launch records at the end. Existing
kernel filesystem checks run for all four roles. Only the app-server's socket
profile additionally forbids unlinking the physical RPC socket.

The read-only inspector records this proof separately. The production task
enrollment and final sender gate repeat it; neither a saved process ID nor the
display's identity substitutes for the original execution server. The sender
also validates the original externally published adoption before proceeding.
STOP remains checked before and after the observations. These reads are not
atomic with a later process change or input.

Network v1 remains byte-compatible. New materializations may explicitly request
`task_callback_network=True` together with `task_model_network=True`. This
freezes `upgrade-task-model-callback-network-v2`, deriving its only Unix socket
exception from the original evidence root:

```
live-control/task-startup/callback-ingress/s
```

Only the two Claude execution profiles receive this exception. Codex's server
still cannot connect to that endpoint. Materialization and validation rebuild
the same exact profile; changing the callback path, provider port or schema does
not expand a previously frozen task. The existing callback protocol, ancestry,
pack, attempt, original input and no-replay checks are unchanged.

Tests use real temporary macOS sandboxed processes and Unix sockets to verify
callback reachability and denial of other broker sockets, including descendants.
Role substitutions, process-change/STOP checks and sender/adoption wiring use
explicit native/kernel fixtures. They do not establish live model adoption.

The final TASK launch refusal remains in place. Callback socket reachability is
only one dependency: the current Claude callback sender also uses cmux tree
queries and original app-server metadata reads. Model-only egress denies those
connections. A bounded read-only observation route must be implemented and
verified without granting arbitrary cmux/Herdr input or app-server RPC. The
automatic hooks that query cmux need the same analysis. Mach/Apple Events,
inherited descriptors and other native helper/broker paths are not proven by
these filesystem checks. Actual adoption must include those live observations;
changing `ready` or accepting an old inspection schema would not supply them.
