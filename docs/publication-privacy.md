# Publication privacy

The public integration uses ordinary forward commits. It removes identified personal-machine paths and a provider endpoint from incoming evidence documents, provenance metadata, a module description and test configuration. External test dependencies use optional `MAC_TEST_*` variables with neutral defaults; they do not embed the maintainer's private directory structure. See [test dependencies](test-dependencies.md).

The two previously published synthetic fixtures remain unchanged:

- `verification/fixtures/claude-c2596-screen-20261008.txt`
- `verification/fixtures/claude-c41-screen-20261008.txt`

Four additional terminal fixtures now contain synthetic public layouts:

- `verification/fixtures/claude-default-a-20261018.txt`
- `verification/fixtures/claude-default-b-20261018.txt`
- `verification/fixtures/claude-busy-goal-split-a-20261026.txt`
- `verification/fixtures/claude-busy-goal-split-b-20261026.txt`

Their parser-relevant composer, footer, activity and goal shapes are retained. Hash assertions refer to the new synthetic bytes; historical hashes remain archive references and are not presented as hashes of the transformed files. Missing external test dependencies remain explicit skips. The stage-path stress input retains the original 68-byte length.

Validation is scoped to the affected parser, dependency-selection, materialization and stage tests. The earlier two-fixture cleanup passed 24 parser tests and its historical 1,290-test offline suite; those old counts do not describe the integrated runtime. Current validation results must be tied to their actual commit. No live terminal input or model request is established by these checks.

Git history has not been rewritten. Older commits and retained branches may still expose removed content. Current-tree replacements and passing tests do not establish deletion of historical copies. The separate whole-history publication decision remains pending; no force-push is part of this integration.

Known-pattern inspection is limited to the identified material. It does not prove absence of every private narrative, credential or personal identifier. The earlier historical Gitleaks finding for the protocol identifier `executor-reply-consumer-proof-v1` was a false positive, not an authentication secret; that conclusion does not certify either this tree or all history.
