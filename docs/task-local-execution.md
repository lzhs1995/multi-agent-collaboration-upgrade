# Typed local execution and scheduling

In the TASK topology, `claim` reserves the original node but does not execute it.
The model selects the frozen `local-execute` ID in its own native tool call.
The retained actor checks its live enrollment, original RPC and durable intent,
then runs precisely the original workload argv, request and output root. The
node lock stays held; the shared coordinator lock is released while the subprocess
works so other lanes can publish results. STOP and claim identity are checked
again after intent and immediately before execution.

The subprocess's real return code and streams are recorded in the original
operation journal. They are not represented as a synthetic native shell call.
The native reader joins the model's exact typed invocation, original ledger/RPC
completion, original outer result and stage evidence. A nonzero workload return
is a failed task action even when the dynamic tool itself returned normally.
Missing results remain pending; a consumed or interrupted ID cannot execute again.
Candidate creation, independent checks and explicit acceptance remain separate.

For TASK campaigns, B-arm `advance` returns scheduling proposals and executes
zero business actions. Each proposal names an unconsumed operation in the current
turn's original scope and its exact native script. The model must invoke each
selected operation separately; the observer cannot impersonate another operation
or renew its budget. A-arm native scheduling and legacy foreground `advance`
retain their original behavior. Review and acceptance are never automated by this
observation. The original run deadlines still apply; observations grant no time.

The tests use real temporary SQLite journals, workload subprocesses and an
independent corpus checker, with explicitly synthetic native/model/kernel and
adoption fixtures. These results do not establish live task adoption, full broker
or inherited-FD isolation, real model execution, or experiment acceptance. Formal
TASK launch remains disabled until those deployment boundaries are complete.
