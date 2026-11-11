# Optional test dependencies

The offline suite can use separately pinned local fixtures through these test-only variables. No production runtime reads them.

| Variable | Expected path | Neutral default |
|---|---|---|
| `MAC_TEST_MAC084_SOURCE` | Complete pinned MAC .84 source directory | `/opt/collaboration/test-fixtures/mac084/source` |
| `MAC_TEST_NODE` | Pinned native Node executable | `/opt/collaboration/test-fixtures/node` |
| `MAC_TEST_JEV_PLAN` | Original frozen JEV plan JSON | `/opt/collaboration/test-fixtures/jev-plan.json` |
| `MAC_TEST_CODEX_BINARY` | Pinned Codex executable for socket fixtures | `/opt/collaboration/test-fixtures/codex` |
| `MAC_TEST_CODEX_PACKAGE` | Complete pinned Codex package directory | `/opt/collaboration/test-fixtures/codex-package` |

Set only the variables for dependencies you actually hold and verify their original registered hashes. The original dependency-existence guards and identity checks still apply. Missing dependencies are reported as skips, not passing executions. These variables neither create dependencies nor authorize network/model requests or live session changes.

Run `python3 -B scripts/test_all.py` to use the established `OfflineResult` isolation. Keep private path bindings and task-specific runners outside the public repository. Compare actual test counts, skipped test identifiers and reasons when reproducing a registered run.
