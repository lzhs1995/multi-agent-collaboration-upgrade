# TASK integration CI compatibility

The launch planner and schedule driver share read-only owner contracts through
`upgrade_task_owner_contract.py`. The two functions were moved without changing
their checks. The executable owner and its private dynamic import are excluded
from the business adapter closure. Explicit v3 advice controls still include
the worker and publisher, as documented in `task-actor-integration-candidate.md`;
those frozen bytes do not grant a connection or operation budget.

Offline reader/parser/journal tests use the exact 0.4.84 source subset in
`tests/fixtures/original-controller-084`, checking its manifest before use. This
lets CI run the tests without the developer's local history directories. Full
materialization continues to require complete local controller/client packages;
the source subset is neither installable nor a replacement registered controller.

Tests that inspect their own Darwin child processes explicitly retain the real
kernel reader instead of inheriting the suite's ordinary-client mock. Linux
still runs the portable socket and original-reader tests; Darwin-only kernel
integration is skipped there. Nested fixture cleanup is registered before setup,
and the shared target fixture cleans up even when called directly and setup fails.
The observation-source fixture patches the module actually used by the reader.

These checks exercise source dependencies, local processes, sockets, generated
readers and fixture RPC/results. They do not establish live model adoption,
dual-Claude business completion or benchmark results.
