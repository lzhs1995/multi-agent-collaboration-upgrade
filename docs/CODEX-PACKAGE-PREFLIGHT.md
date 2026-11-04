# Codex package preflight for new experiments

The original j6 startup timed out after its Codex main executable launched but
could not start a missing `codex-code-mode-host`. Its exact helper was restored,
but the original client never proved a successful tool call within that attempt.
The original timeout, controller and budgets remain unchanged.

New `upgrade_live_materialize.py` CLI preparations require `--codex-package`.
Provide the canonical complete package directory and the canonical executable
selected from it. Preparation verifies that `bin/codex` resolves to that exact
executable, that `bin/codex-code-mode-host` is executable, and that the direct
executable's adjacent host resolves to the same helper. It records every regular
file's full SHA, mode and size, plus directories and symlink targets. It rejects
escaping links, directory links, special files, unsupported layouts and missing
required components before creating any arm artifacts. It does not copy or run
the package, create an installation or change the system's client selector.

The frozen binding carries this contract. The complete startup validator checks
it again, including when used by the generated private controller. Each lane
also rechecks after any pre-exec hold returns and before starting its server or
native client; a package that disappeared while held cannot use the earlier
successful check. These are point-in-time checks, not a filesystem lock against
changes after validation. A missing or
changed helper, resource, link or package member refuses admission before native
startup. Existing controllers do not acquire this field or change behavior. The
Python API retains an optional parameter only for old callers and fixtures; a
new caller must explicitly pass the package to obtain this protection. It must
not claim package coverage when the contract is absent.

This is a local content and path check. It does not independently establish that
an archive is an authentic or complete publisher release: verify the official
archive digest and signatures through the existing client updater first. It
also does not prove real code-mode execution, automatic hooks, model capacity,
callback delivery or efficiency. Keep the registered real readiness probe and
adoption gate. The current resolver supports direct package executables with an
adjacent helper; app-bundle shell launchers refuse rather than being executed to
guess their target.

`test_upgrade_codex_package.py` exercises actual filesystem loss and changes,
the materializer's no-arm refusal, actual frozen-module imports and startup
rechecks, helper loss during a hold in all three lanes, and old API behavior.
No test starts an agent or replays j6. Full-package hashing adds preparation and
validation cost; do not omit it from future measured cold-start timing.
