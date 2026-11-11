# Original observer reads during startup

The explicit observer-network-v3 contract requires each startup shell probe and
matching automatic PreToolUse/PostToolUse hook to perform a real read through
the fixed observer endpoint. Legacy contracts keep their existing requirements.

The probe prints success only after its exact tree request and response have
been read back. Hook records retain the observer receipt alongside the actual
hook PID and ancestry. Readiness joins the original native call/result and hook
records to those receipts; it rejects a missing receipt, copied path, changed
nonce, foreign lane, startup or process chain. The generated private controller
ships the same reader and does not import the external lifecycle owner.

Historical receipt verification remains possible after the observer closes.
It proves a past read, not current liveness, direct RPC ownership, model
consumption, adoption or permission to send input. Original launch and runtime
records must still match. The evidence reader does not issue a new request.

Tests exercise a real Unix observer and child process, the actual probe entry,
historical reading and rejection cases, and the rendered private reader in an
isolated interpreter. Deployment and native-hook-origin fixtures are explicit;
these tests do not establish real model calls or full TASK adoption.
